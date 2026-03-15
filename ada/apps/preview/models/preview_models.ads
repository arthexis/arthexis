with Arthexis.ORM;

package Apps.Preview.Models.Preview_Models is
   --  Schema objects owned by the Preview app.

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection);
   --  Create preview snapshot request tables and indexes.
end Apps.Preview.Models.Preview_Models;
